"""Process orchestration: runs proxy, backend, web UI, tray, and updater.

Three entry points are supported:

* :func:`run_daemon` -- headless asyncio loop, waits for SIGINT/SIGTERM.
* :func:`run_with_tray` -- starts asyncio in a background thread and runs
  the GTK main loop on the main thread so the tray stays responsive.
* :func:`daemonize` -- classic double-fork that detaches from the
  controlling terminal, redirects stdio to the log file, and writes a
  pidfile. Combined with :func:`run_daemon` this gives a true background
  mode: ``droidproxy daemon --detach``. Use :func:`stop_daemon` and
  :func:`daemon_status` from ``cli.py`` to manage it.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from droidproxy.context import AppContext
from droidproxy.paths import icon_path, log_file, state_dir
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
                "Could not install cli-proxy-api automatically: %s", err
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


# ---------------------------------------------------------------------------
# Daemonization (double-fork) + pidfile management
# ---------------------------------------------------------------------------


def pidfile_path() -> Path:
    """Where the detached daemon writes its pid.

    Prefers ``$XDG_RUNTIME_DIR/droidproxy.pid`` (volatile tmpfs on most
    distros, wiped on logout, which is the right thing for a user service).
    Falls back to the state dir when ``XDG_RUNTIME_DIR`` is unset -- typically
    inside a ``systemd --user`` unit that clears the env, or on a system
    without user runtime dirs.
    """
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "droidproxy.pid"
    return state_dir() / "droidproxy.pid"


def _read_pidfile() -> int | None:
    path = pidfile_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it (shouldn't happen for our
        # own user's pid, but handle it anyway).
        return True
    # Zombie-reap path: if the pid is a child of ours that has exited but
    # not yet been waited on, os.kill(pid, 0) still succeeds. waitpid with
    # WNOHANG returns the pid in that case, which is our signal that the
    # process is gone. If the pid is not our child, waitpid raises ECHILD,
    # which we ignore. This makes _is_alive correct whether the daemon is
    # orphaned (reaped by init, normal --detach flow) or a direct child
    # (under test, or started without --detach).
    try:
        reaped_pid, _ = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    return True


def _write_pidfile(pid: int) -> Path:
    path = pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp + rename.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(f"{pid}\n")
    tmp.replace(path)
    return path


def _remove_pidfile() -> None:
    try:
        pidfile_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def daemonize(log_path: Path | None = None) -> None:
    """Double-fork into the background; parent exits inside this call.

    On return, only the grandchild process is alive. Its stdin is
    ``/dev/null``, stdout and stderr go to ``log_path`` (default: the
    rotating log file under ``$XDG_STATE_HOME/droidproxy/``), it is a
    session leader with no controlling terminal, and its pid has been
    written to :func:`pidfile_path`.

    Raises :class:`SystemExit` with a non-zero status if another live
    daemon is already running.
    """
    existing = _read_pidfile()
    if existing is not None and _is_alive(existing):
        print(
            f"droidproxy is already running (pid {existing}). "
            f"Use `droidproxy stop` to terminate it first.",
            file=sys.stderr,
        )
        sys.exit(1)
    if existing is not None:
        # Stale pidfile: the old process is gone, reclaim it.
        _remove_pidfile()

    # First fork: detach from shell
    if os.fork() != 0:
        os._exit(0)

    # Become session leader so we don't inherit the shell's process group
    os.setsid()

    # Second fork: ensure we can't reacquire a controlling terminal
    if os.fork() != 0:
        os._exit(0)

    # Change to a stable directory so we don't hold any CWD busy
    os.chdir("/")
    os.umask(0o022)

    # Redirect stdio: devnull in, log file out+err
    path = log_path or log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    devnull_fd = os.open(os.devnull, os.O_RDONLY)
    log_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(devnull_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(devnull_fd)
    os.close(log_fd)

    _write_pidfile(os.getpid())
    atexit.register(_remove_pidfile)


def stop_daemon(timeout: float = 5.0) -> int:
    """Send SIGTERM to the detached daemon and wait for it to exit.

    Returns a shell-style exit code: 0 on success, 1 on "not running",
    2 on "force-killed after timeout".
    """
    pid = _read_pidfile()
    if pid is None:
        print("droidproxy is not running (no pidfile)", file=sys.stderr)
        return 1
    if not _is_alive(pid):
        print(f"droidproxy pidfile points at dead pid {pid}; cleaning up")
        _remove_pidfile()
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _remove_pidfile()
        print(f"droidproxy already gone (pid {pid})")
        return 1

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            _remove_pidfile()
            print(f"droidproxy stopped (pid {pid})")
            return 0
        time.sleep(0.1)

    # Graceful shutdown didn't complete in time: escalate
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _remove_pidfile()
    print(f"droidproxy force-killed after {timeout}s timeout (pid {pid})")
    return 2


def daemon_status() -> int:
    """Report whether the detached daemon is running.

    Exit codes follow the LSB service-status convention:
    0 running, 3 stopped, 4 stale pidfile.
    """
    pid = _read_pidfile()
    if pid is None:
        print("droidproxy: stopped")
        return 3
    if _is_alive(pid):
        print(f"droidproxy: running (pid {pid}, pidfile {pidfile_path()})")
        return 0
    print(f"droidproxy: stale pidfile (pid {pid} no longer exists)")
    return 4
