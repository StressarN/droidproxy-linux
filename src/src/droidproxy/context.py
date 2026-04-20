"""Runtime wiring shared across the web UI, tray, and process lifecycle.

The :class:`AppContext` owns long-lived singletons (preferences, auth
manager, server manager, thinking proxy, tunnel). Both the web UI and the
GTK tray depend on this context so that they can't disagree about state.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from droidproxy.auth import AuthManager, AuthWatcher
from droidproxy.backend import ServerManager
from droidproxy.paths import cli_proxy_api_binary
from droidproxy.prefs import PreferencesStore, get_store
from droidproxy.proxy import ProxyConfig, ThinkingProxy

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    prefs: PreferencesStore
    auth_manager: AuthManager
    auth_watcher: AuthWatcher
    server: ServerManager
    proxy: ThinkingProxy
    loop: asyncio.AbstractEventLoop
    tunnel: Any = None  # TunnelManager, injected by cli.py
    updater: Any = None  # set by cli.py

    @classmethod
    def build(
        cls,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        prefs_store: PreferencesStore | None = None,
        proxy_config: ProxyConfig | None = None,
    ) -> AppContext:
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
        prefs = prefs_store or get_store()
        auth_manager = AuthManager()
        auth_watcher = AuthWatcher(auth_manager, loop=loop)
        server = ServerManager(
            port=(proxy_config.upstream_port if proxy_config else 8318),
            prefs_store=prefs,
            binary_path=cli_proxy_api_binary(),
        )
        proxy = ThinkingProxy(config=proxy_config, prefs_store=prefs)
        return cls(
            prefs=prefs,
            auth_manager=auth_manager,
            auth_watcher=auth_watcher,
            server=server,
            proxy=proxy,
            loop=loop,
        )

    async def start(self) -> None:
        """Start proxy, backend, and auth watcher in the correct order."""
        await self.proxy.start()
        await self.server.start()
        self.auth_watcher.start()

    async def stop(self) -> None:
        self.auth_watcher.stop()
        await self.proxy.stop()
        await self.server.stop()
        if self.tunnel is not None:
            try:
                await self.tunnel.stop()
            except Exception:
                log.debug("tunnel.stop raised", exc_info=True)

    @property
    def proxy_url(self) -> str:
        return f"http://localhost:{self.proxy.proxy_port}"
