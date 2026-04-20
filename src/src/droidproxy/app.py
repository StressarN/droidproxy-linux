"""Process orchestration: runs proxy, backend, web UI, tray, and updater.

Two entry points are supported:

* :func:`run_daemon` -- headless asyncio loop, waits for SIGINT/SIGTERM.
* :func:`run_with_tray` -- starts asyncio in a background thread and runs
  the GTK main loop on the main thread so the tray stays responsive.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
from dataclasses import dataclass
from pathlib import Path

from droidproxy.context import AppContext
from droidproxy.paths import icon_path
from droidproxy.proxy import ProxyConfig
from droidproxy.tunnel import TunnelManager
from droidproxy.updater import Updater
from droidproxy.web import WebUI

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppOptions:
    web_host: str = "127.0.0.1"
    web_port: int = 8316
    proxy_config: ProxyConfig = ProxyConfig()
    auto_download_binary: bool = True


class _LoopThread:
    """Run an asyncio loop on a dedicated thread and block until ready."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    def start(self) -> asyncio.AbstractEventLoop:
        def target() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop
            self._started.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._thread = threading.Thread(
            target=target, name="droidproxy-loop", daemon=True
        )
        self._thread.start()
        self._started.wait()
        assert self.loop is not None
        return self.loop

    def stop(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)


def _build_services(
    loop: asyncio.AbstractEventLoop, options: AppOptions
) -> tuple[AppContext, WebUI]:
    context = AppContext.build(loop=loop, proxy_config=options.proxy_config)
    context.tunnel = TunnelManager()
    context.updater = Updater()
    web = WebUI(
        context, host=options.web_host, port=options.web_port
    )
    return context, web


async def _start_services(
    context: AppContext, web: WebUI, *, auto_download_binary: bool
) -> None:
    if auto_download_binary:
        from droidproxy import binary  # local import -- avoids fetch on headless boots

        try:
            await asyncio.to_thread(binary.ensure_installed)
        except binary.BinaryError as err:
            log.warning(
                "Could not install cli-proxy-api-plus automatically: %s", err
            )
    await context.start()
    await web.start()
    try:
        await context.updater.start()  # type: ignore[union-attr]
    except Exception:
        log.debug("Updater failed to start", exc_info=True)


async def _stop_services(context: AppContext, web: WebUI) -> None:
    try:
        await web.stop()
    except Exception:
        log.debug("web.stop raised", exc_info=True)
    try:
        if context.updater is not None:
            await context.updater.stop()
    except Exception:
        log.debug("updater.stop raised", exc_info=True)
    await context.stop()


def run_daemon(options: AppOptions | None = None) -> int:
    """Headless run loop. Blocks until SIGINT/SIGTERM."""
    if options is None:
        options = AppOptions()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    context, web = _build_services(loop, options)

    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: _request_stop())

    try:
        loop.run_until_complete(
            _start_services(context, web, auto_download_binary=options.auto_download_binary)
        )
    except Exception as err:  # noqa: BLE001
        log.error("Failed to start services: %s", err)
        loop.run_until_complete(_stop_services(context, web))
        loop.close()
        return 1

    log.info("Daemon ready. Web UI at %s", web.url)

    try:
        loop.run_until_complete(stop_event.wait())
    finally:
        loop.run_until_complete(_stop_services(context, web))
        loop.close()
    return 0


def run_with_tray(options: AppOptions | None = None) -> int:
    """Start asyncio in the background and run the GTK tray on the main thread."""
    if options is None:
        options = AppOptions()
    from droidproxy import tray

    loop_thread = _LoopThread()
    loop = loop_thread.start()
    context, web = _build_services(loop, options)

    future = asyncio.run_coroutine_threadsafe(
        _start_services(context, web, auto_download_binary=options.auto_download_binary),
        loop,
    )
    try:
        future.result(timeout=20)
    except Exception as err:  # noqa: BLE001
        log.error("Failed to start services: %s", err)
        _shutdown(loop, context, web)
        loop_thread.stop()
        return 1

    icon_active = _maybe_icon("icon-active.png")
    icon_inactive = _maybe_icon("icon-inactive.png")
    tray_app = tray.TrayApp(
        context,
        settings_url=web.url,
        icon_active=icon_active,
        icon_inactive=icon_inactive,
    )

    # Poll proxy/server state periodically so tray labels stay current even
    # when the user toggles things through the web UI.
    def _kick_refresh() -> bool:
        tray_app.notify_state_changed()
        return True

    try:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        GLib.timeout_add_seconds(2, _kick_refresh)
    except Exception:
        pass

    try:
        tray_app.run()
    finally:
        _shutdown(loop, context, web)
        loop_thread.stop()
    return 0


def _shutdown(
    loop: asyncio.AbstractEventLoop, context: AppContext, web: WebUI
) -> None:
    future = asyncio.run_coroutine_threadsafe(_stop_services(context, web), loop)
    try:
        future.result(timeout=10)
    except Exception:
        log.debug("shutdown coroutine failed", exc_info=True)


def _maybe_icon(name: str) -> Path | str | None:
    path = icon_path(name)
    if path.exists():
        return str(path)
    return None
