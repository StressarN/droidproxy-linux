from __future__ import annotations

import sys

import pytest

from droidproxy import tray


def test_tray_module_imports_without_gtk(monkeypatch: pytest.MonkeyPatch) -> None:
    """``tray.py`` must import on headless boxes. GTK only loads in ``run()``."""
    # Force the gi import to fail inside _import_gtk.
    monkeypatch.setitem(sys.modules, "gi", None)
    with pytest.raises(tray.TrayUnavailableError):
        tray._import_gtk()


def test_tray_app_construction_does_not_require_gtk() -> None:
    class StubProxy:
        proxy_port = 8317
        is_running = False

    class StubServer:
        is_running = False

    class StubContext:
        proxy = StubProxy()
        server = StubServer()
        proxy_url = "http://localhost:8317"
        updater = None
        loop = None

        async def stop(self) -> None:
            return None

    app = tray.TrayApp(StubContext(), settings_url="http://localhost:8316/")
    assert app._settings_url == "http://localhost:8316/"
