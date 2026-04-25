"""GTK system-tray wrapper powered by AyatanaAppIndicator3.

Mirrors the NSStatusItem menu in ``src/Sources/AppDelegate.swift``:

* Server status row
* Open Settings (launches default browser to the web UI URL)
* Start / Stop Server
* Copy Server URL
* Open Dashboard (cli-proxy-api management UI on :8318)
* Check for Updates
* Quit

The tray imports PyGObject lazily so ``droidproxy --daemon`` works on
headless systems without the GTK runtime installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import webbrowser
from pathlib import Path

log = logging.getLogger(__name__)


class TrayUnavailableError(RuntimeError):
    """Raised when GTK / AppIndicator is not available on the host."""


def _import_gtk():
    try:
        import gi
    except ImportError as err:
        raise TrayUnavailableError("PyGObject is not installed") from err

    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except (ValueError, ImportError):
        try:
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        except (ValueError, ImportError) as err:
            raise TrayUnavailableError(
                "No AppIndicator library available; install libayatana-appindicator"
            ) from err

    from gi.repository import GLib, Gtk

    return Gtk, GLib, AppIndicator


class TrayApp:
    """Tray icon + menu bound to an :class:`AppContext`.

    The asyncio loop lives in a background thread; menu actions marshal
    work back onto that loop via :meth:`asyncio.AbstractEventLoop.call_soon_threadsafe`.
    """

    def __init__(
        self,
        context,
        *,
        settings_url: str,
        dashboard_url: str = "http://localhost:8318/management.html",
        icon_active: Path | str | None = None,
        icon_inactive: Path | str | None = None,
    ) -> None:
        self._ctx = context
        self._settings_url = settings_url
        self._dashboard_url = dashboard_url
        self._icon_active = str(icon_active) if icon_active else None
        self._icon_inactive = str(icon_inactive) if icon_inactive else None
        self._Gtk = None
        self._GLib = None
        self._AppIndicator = None
        self._indicator = None
        self._menu = None
        self._item_status = None
        self._item_server_toggle = None
        self._item_copy_url = None
        self._item_dashboard = None

    def run(self) -> None:
        self._Gtk, self._GLib, self._AppIndicator = _import_gtk()
        self._build_indicator()
        self._refresh_ui()
        self._Gtk.main()

    def quit(self) -> None:
        if self._Gtk is not None:
            self._Gtk.main_quit()

    def notify_state_changed(self) -> None:
        """Signal from other threads that server/proxy state has changed."""
        if self._GLib is None:
            return
        self._GLib.idle_add(self._refresh_ui)

    # --- internals ----------------------------------------------------------

    def _build_indicator(self) -> None:
        Gtk = self._Gtk
        AppIndicator = self._AppIndicator

        assert Gtk is not None and AppIndicator is not None
        icon = self._icon_inactive or "network-offline"
        indicator = AppIndicator.Indicator.new(
            "droidproxy-linux",
            icon,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()

        self._item_status = Gtk.MenuItem(label="Server: Stopped")
        self._item_status.set_sensitive(False)
        menu.append(self._item_status)
        menu.append(Gtk.SeparatorMenuItem())

        settings_item = Gtk.MenuItem(label="Open Settings")
        settings_item.connect("activate", lambda _w: self._open(self._settings_url))
        menu.append(settings_item)

        menu.append(Gtk.SeparatorMenuItem())

        self._item_server_toggle = Gtk.MenuItem(label="Start Server")
        self._item_server_toggle.connect("activate", self._on_toggle_server)
        menu.append(self._item_server_toggle)

        menu.append(Gtk.SeparatorMenuItem())

        self._item_copy_url = Gtk.MenuItem(label="Copy Server URL")
        self._item_copy_url.connect("activate", self._on_copy_url)
        menu.append(self._item_copy_url)

        self._item_dashboard = Gtk.MenuItem(label="Open Dashboard")
        self._item_dashboard.connect(
            "activate", lambda _w: self._open(self._dashboard_url)
        )
        menu.append(self._item_dashboard)

        menu.append(Gtk.SeparatorMenuItem())

        updates_item = Gtk.MenuItem(label="Check for Updates...")
        updates_item.connect("activate", self._on_check_updates)
        menu.append(updates_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit)
        menu.append(quit_item)

        menu.show_all()
        indicator.set_menu(menu)

        self._indicator = indicator
        self._menu = menu

    def _refresh_ui(self) -> bool:
        if self._indicator is None:
            return False
        running = self._ctx.server.is_running and self._ctx.proxy.is_running
        icon = (self._icon_active if running else self._icon_inactive) or (
            "network-transmit-receive" if running else "network-offline"
        )
        self._indicator.set_icon_full(icon, "DroidProxy")
        if self._item_status is not None:
            self._item_status.set_label(
                f"Server: Running (port {self._ctx.proxy.proxy_port})" if running else "Server: Stopped"
            )
        if self._item_server_toggle is not None:
            self._item_server_toggle.set_label("Stop Server" if running else "Start Server")
        if self._item_copy_url is not None:
            self._item_copy_url.set_sensitive(running)
        if self._item_dashboard is not None:
            self._item_dashboard.set_sensitive(running)
        return False  # idle_add: do not repeat

    def _on_toggle_server(self, _widget) -> None:
        coro = (
            self._stop_server_async() if self._ctx.server.is_running else self._start_server_async()
        )
        self._schedule(coro)

    async def _start_server_async(self) -> None:
        await self._ctx.proxy.start()
        await self._ctx.server.start()
        self._GLib.idle_add(self._refresh_ui)

    async def _stop_server_async(self) -> None:
        await self._ctx.server.stop()
        await self._ctx.proxy.stop()
        self._GLib.idle_add(self._refresh_ui)

    def _on_copy_url(self, _widget) -> None:
        url = self._ctx.proxy_url
        try:
            Gtk = self._Gtk
            clipboard = Gtk.Clipboard.get_default(Gtk.gdk_get_default_root_window().get_display())  # type: ignore[attr-defined]
            clipboard.set_text(url, -1)
            clipboard.store()
        except Exception:
            subprocess.run(
                ["wl-copy"],
                input=url.encode("utf-8"),
                check=False,
            )

    def _on_check_updates(self, _widget) -> None:
        updater = self._ctx.updater
        if updater is None:
            self._open("https://github.com/StressarN/droidproxy-linux/releases/latest")
            return
        self._schedule(updater.check_for_updates(interactive=True))

    def _on_quit(self, _widget) -> None:
        self._schedule(self._quit_async())

    async def _quit_async(self) -> None:
        try:
            await self._ctx.stop()
        finally:
            self._GLib.idle_add(lambda: (self.quit(), False)[1])

    def _schedule(self, coro) -> None:
        loop = self._ctx.loop
        if loop is None:
            return
        loop.call_soon_threadsafe(asyncio.ensure_future, coro)

    def _open(self, url: str) -> None:
        # Prefer xdg-open so the user's default handler decides, avoiding
        # webbrowser's own preference for $BROWSER that isn't always set on
        # Wayland/Hyprland systems.
        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env=os.environ.copy(),
            )
        except FileNotFoundError:
            webbrowser.open(url)
