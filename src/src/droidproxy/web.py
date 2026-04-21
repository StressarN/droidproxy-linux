"""Local HTTP settings UI served on 127.0.0.1:8316.

This replaces the SwiftUI ``SettingsView``. The same logical sections are
exposed: accounts, per-model effort pickers, Max Budget, fast modes,
provider toggles, remote-management, tunnel controls, and a log tail.

The UI consumes ``/api/*`` JSON endpoints. Live updates for auth state and
backend logs stream over Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aiohttp import web

from droidproxy.auth import ServiceType
from droidproxy.backend import AuthCommand
from droidproxy.context import AppContext
from droidproxy.paths import web_assets_dir

log = logging.getLogger(__name__)

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8316


async def _sse_event(resp: web.StreamResponse, payload: Any) -> None:
    data = json.dumps(payload, default=str)
    await resp.write(f"data: {data}\n\n".encode())


class WebUI:
    """aiohttp web application wrapping the settings HTTP API."""

    def __init__(
        self,
        context: AppContext,
        *,
        host: str = DEFAULT_BIND,
        port: int = DEFAULT_PORT,
        assets_dir: Path | None = None,
    ) -> None:
        self._ctx = context
        self._host = host
        self._port = port
        self._assets = assets_dir or web_assets_dir()
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}/"

    async def start(self) -> None:
        if self._site is not None:
            return
        app = self.build_app()
        self._runner = web.AppRunner(app, handle_signals=False)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self._host, port=self._port, reuse_address=True)
        await self._site.start()
        log.info("Settings UI listening on %s", self.url)

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/styles.css", self._asset_factory("styles.css", "text/css"))
        app.router.add_get("/app.js", self._asset_factory("app.js", "application/javascript"))
        app.router.add_get("/logo.png", self._asset_factory("../logo.png", "image/png"))

        app.router.add_get("/api/status", self._get_status)
        app.router.add_get("/api/prefs", self._get_prefs)
        app.router.add_patch("/api/prefs", self._patch_prefs)
        app.router.add_post(
            "/api/prefs/providers/{name}", self._toggle_provider
        )

        app.router.add_get("/api/auth", self._get_auth)
        app.router.add_post("/api/auth/{service}/login", self._start_auth_login)
        app.router.add_post(
            "/api/auth/{service}/{account_id}/toggle", self._toggle_auth_account
        )
        app.router.add_delete(
            "/api/auth/{service}/{account_id}", self._delete_auth_account
        )
        app.router.add_get("/api/auth/stream", self._stream_auth)

        app.router.add_get("/api/server/status", self._server_status)
        app.router.add_post("/api/server/start", self._server_start)
        app.router.add_post("/api/server/stop", self._server_stop)

        app.router.add_get("/api/logs", self._get_logs)
        app.router.add_get("/api/logs/stream", self._stream_logs)

        app.router.add_post("/api/tunnel/start", self._tunnel_start)
        app.router.add_post("/api/tunnel/stop", self._tunnel_stop)
        app.router.add_get("/api/tunnel/status", self._tunnel_status)

        app.router.add_post("/api/droids/install", self._install_droids)
        app.router.add_get("/api/factory/models/status", self._factory_models_status)
        app.router.add_post("/api/factory/models/apply", self._apply_factory_models)

        return app

    # --- handlers -----------------------------------------------------------

    async def _index(self, request: web.Request) -> web.Response:
        path = self._assets / "index.html"
        if not path.exists():
            return web.Response(status=500, text="UI assets are missing")
        return web.Response(
            body=path.read_bytes(),
            content_type="text/html",
            charset="utf-8",
        )

    def _asset_factory(self, relative: str, content_type: str):
        async def handler(request: web.Request) -> web.StreamResponse:
            path = (self._assets / relative).resolve()
            if not path.exists() or not str(path).startswith(str(self._assets.resolve().parent)):
                raise web.HTTPNotFound()
            return web.Response(body=path.read_bytes(), content_type=content_type)

        return handler

    async def _get_status(self, request: web.Request) -> web.Response:
        return web.json_response(self._status_payload())

    async def _get_prefs(self, request: web.Request) -> web.Response:
        return web.json_response(self._ctx.prefs.as_dict())

    async def _patch_prefs(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError as err:
            raise web.HTTPBadRequest(reason="Invalid JSON body") from err
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(reason="Expected a JSON object")
        try:
            applied = self._ctx.prefs.update(payload)
        except (KeyError, ValueError) as err:
            raise web.HTTPBadRequest(reason=str(err)) from err
        return web.json_response({"applied": applied})

    async def _toggle_provider(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        try:
            payload = await request.json()
        except json.JSONDecodeError as err:
            raise web.HTTPBadRequest(reason="Invalid JSON body") from err
        enabled = bool(payload.get("enabled", True)) if isinstance(payload, dict) else True
        try:
            new_state = self._ctx.prefs.set_provider_enabled(name, enabled)
        except ValueError as err:
            raise web.HTTPBadRequest(reason=str(err)) from err
        return web.json_response({"provider": name, "enabled": new_state})

    async def _get_auth(self, request: web.Request) -> web.Response:
        self._ctx.auth_manager.refresh()
        return web.json_response(self._ctx.auth_manager.snapshot())

    async def _start_auth_login(self, request: web.Request) -> web.Response:
        service = request.match_info["service"]
        try:
            command = AuthCommand(service)
        except ValueError as err:
            raise web.HTTPBadRequest(reason=f"Unknown service: {service}") from err
        outcome = await self._ctx.server.run_auth_command(command)
        status = 200 if outcome.success else 500
        return web.json_response(asdict(outcome), status=status)

    async def _toggle_auth_account(self, request: web.Request) -> web.Response:
        account_id = request.match_info["account_id"]
        ok = self._ctx.auth_manager.toggle_disabled(account_id)
        return web.json_response({"ok": ok})

    async def _delete_auth_account(self, request: web.Request) -> web.Response:
        account_id = request.match_info["account_id"]
        ok = self._ctx.auth_manager.delete_account(account_id)
        return web.json_response({"ok": ok})

    async def _stream_auth(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)

        async def listener(snapshot: dict[str, Any]) -> None:
            try:
                queue.put_nowait(snapshot)
            except asyncio.QueueFull:
                pass

        self._ctx.auth_watcher.add_listener(listener)
        try:
            self._ctx.auth_manager.refresh()
            await _sse_event(resp, self._ctx.auth_manager.snapshot())
            while not request.transport.is_closing():  # type: ignore[union-attr]
                try:
                    snapshot = await asyncio.wait_for(queue.get(), timeout=20.0)
                except TimeoutError:
                    await resp.write(b": heartbeat\n\n")
                    continue
                await _sse_event(resp, snapshot)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._ctx.auth_watcher.remove_listener(listener)
        return resp

    async def _server_status(self, request: web.Request) -> web.Response:
        return web.json_response(self._status_payload())

    async def _server_start(self, request: web.Request) -> web.Response:
        await self._ctx.proxy.start()
        ok = await self._ctx.server.start()
        return web.json_response({"running": ok, **self._status_payload()})

    async def _server_stop(self, request: web.Request) -> web.Response:
        await self._ctx.server.stop()
        await self._ctx.proxy.stop()
        return web.json_response(self._status_payload())

    async def _get_logs(self, request: web.Request) -> web.Response:
        return web.json_response({"lines": self._ctx.server.logs()})

    async def _stream_logs(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        queue: asyncio.Queue[list[str]] = asyncio.Queue(maxsize=32)

        def listener(lines: list[str]) -> None:
            try:
                queue.put_nowait(lines)
            except asyncio.QueueFull:
                pass

        self._ctx.server.add_log_listener(listener)
        try:
            await _sse_event(resp, {"lines": self._ctx.server.logs()})
            while not request.transport.is_closing():  # type: ignore[union-attr]
                try:
                    lines = await asyncio.wait_for(queue.get(), timeout=20.0)
                except TimeoutError:
                    await resp.write(b": heartbeat\n\n")
                    continue
                await _sse_event(resp, {"lines": lines})
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._ctx.server.remove_log_listener(listener)
        return resp

    async def _tunnel_start(self, request: web.Request) -> web.Response:
        tunnel = self._ctx.tunnel
        if tunnel is None:
            raise web.HTTPServiceUnavailable(reason="Tunnel manager not configured")
        result = await tunnel.start(self._ctx.proxy.proxy_port)
        return web.json_response(result)

    async def _tunnel_stop(self, request: web.Request) -> web.Response:
        tunnel = self._ctx.tunnel
        if tunnel is None:
            raise web.HTTPServiceUnavailable(reason="Tunnel manager not configured")
        await tunnel.stop()
        return web.json_response({"ok": True})

    async def _tunnel_status(self, request: web.Request) -> web.Response:
        tunnel = self._ctx.tunnel
        if tunnel is None:
            return web.json_response({"available": False})
        return web.json_response(
            {"available": True, "running": tunnel.is_running, "url": tunnel.public_url}
        )

    async def _install_droids(self, request: web.Request) -> web.Response:
        from droidproxy.installer import install_challenger_droids  # local import

        result = install_challenger_droids()
        return web.json_response(result)

    async def _factory_models_status(self, request: web.Request) -> web.Response:
        from droidproxy.installer import (
            DROID_PROXY_MODELS,
            factory_custom_models_installed,
            factory_settings_path,
        )

        prefs = self._ctx.prefs.snapshot()
        installed = factory_custom_models_installed(prefs.enabled_providers)
        return web.json_response(
            {
                "installed": installed,
                "settings_path": str(factory_settings_path()),
                "model_ids": [m["id"] for m in DROID_PROXY_MODELS],
            }
        )

    async def _apply_factory_models(self, request: web.Request) -> web.Response:
        from droidproxy.installer import install_factory_custom_models

        prefs = self._ctx.prefs.snapshot()
        try:
            result = install_factory_custom_models(prefs.enabled_providers)
        except OSError as err:
            raise web.HTTPInternalServerError(reason=str(err)) from err
        return web.json_response(result)

    def _status_payload(self) -> dict[str, Any]:
        from droidproxy.installer import (
            factory_custom_models_installed,
            factory_settings_path,
        )

        prefs = self._ctx.prefs.as_dict()
        snap = self._ctx.prefs.snapshot()
        return {
            "server_running": self._ctx.server.is_running,
            "proxy_running": self._ctx.proxy.is_running,
            "proxy_url": self._ctx.proxy_url,
            "proxy_port": self._ctx.proxy.proxy_port,
            "prefs": prefs,
            "accounts": self._ctx.auth_manager.snapshot(),
            "factory": {
                "models_installed": factory_custom_models_installed(snap.enabled_providers),
                "settings_path": str(factory_settings_path()),
            },
            "effort_options": {
                "opus47_thinking_effort": list(_EFFORT_OPTIONS["opus47_thinking_effort"]),
                "opus46_thinking_effort": list(_EFFORT_OPTIONS["opus46_thinking_effort"]),
                "opus45_thinking_effort": list(_EFFORT_OPTIONS["opus45_thinking_effort"]),
                "sonnet46_thinking_effort": list(_EFFORT_OPTIONS["sonnet46_thinking_effort"]),
                "gpt53_codex_reasoning_effort": list(
                    _EFFORT_OPTIONS["gpt53_codex_reasoning_effort"]
                ),
                "gpt54_reasoning_effort": list(_EFFORT_OPTIONS["gpt54_reasoning_effort"]),
                "gemini31_pro_thinking_level": list(
                    _EFFORT_OPTIONS["gemini31_pro_thinking_level"]
                ),
                "gemini3_flash_thinking_level": list(
                    _EFFORT_OPTIONS["gemini3_flash_thinking_level"]
                ),
            },
        }


_EFFORT_OPTIONS = {
    "opus47_thinking_effort": ("low", "medium", "high", "xhigh", "max"),
    "opus46_thinking_effort": ("low", "medium", "high", "max"),
    "opus45_thinking_effort": ("low", "medium", "high", "max"),
    "sonnet46_thinking_effort": ("low", "medium", "high", "max"),
    "gpt53_codex_reasoning_effort": ("low", "medium", "high", "xhigh"),
    "gpt54_reasoning_effort": ("low", "medium", "high", "xhigh"),
    "gemini31_pro_thinking_level": ("low", "medium", "high"),
    "gemini3_flash_thinking_level": ("minimal", "low", "medium", "high"),
}


SERVICE_ORDER = [ServiceType.CLAUDE, ServiceType.CODEX, ServiceType.GEMINI]
