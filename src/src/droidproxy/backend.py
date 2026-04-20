"""Manage the upstream ``cli-proxy-api-plus`` subprocess.

Port of the macOS ``ServerManager.swift`` class. Responsibilities:

* Merge the bundled ``config.yaml`` with user preferences and write the
  result to ``~/.cli-proxy-api/merged-config.yaml`` (0600).
* Spawn ``cli-proxy-api-plus -config <merged>`` on port 8318, streaming its
  stdout/stderr into a bounded log ring buffer.
* Gracefully stop the process on shutdown (SIGTERM, then SIGKILL after a
  short grace period).
* Run one-shot ``-claude-login`` / ``-codex-login`` / ``-login`` auth
  commands, injecting the same stdin timing the macOS app uses.
* Kill orphaned instances from previous crashes with :mod:`psutil`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import psutil

from droidproxy.paths import (
    bundled_config_yaml,
    cli_proxy_api_binary,
    merged_config_path,
)
from droidproxy.prefs import PreferencesStore, get_store

log = logging.getLogger(__name__)


MAX_LOG_LINES = 1000
GRACEFUL_TERMINATION_TIMEOUT = 2.0
READINESS_CHECK_DELAY = 1.0


class AuthCommand(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"

    @property
    def flag(self) -> str:
        return {
            AuthCommand.CLAUDE: "-claude-login",
            AuthCommand.CODEX: "-codex-login",
            AuthCommand.GEMINI: "-login",
        }[self]


@dataclass(frozen=True)
class AuthOutcome:
    success: bool
    message: str


LogCallback = Callable[[list[str]], Awaitable[None] | None]


class ServerManager:
    """Lifecycle for the ``cli-proxy-api-plus`` Go process."""

    _PROVIDER_OAUTH_KEYS = {"claude": "claude", "codex": "codex", "gemini": "gemini-cli"}

    def __init__(
        self,
        *,
        port: int = 8318,
        prefs_store: PreferencesStore | None = None,
        binary_path: Path | None = None,
    ) -> None:
        self.port = port
        self._prefs_store = prefs_store or get_store()
        self._binary_path = binary_path or cli_proxy_api_binary()
        self._process: asyncio.subprocess.Process | None = None
        self._readers: list[asyncio.Task[None]] = []
        self._log_buffer: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self._log_listeners: list[LogCallback] = []
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def logs(self) -> list[str]:
        return list(self._log_buffer)

    def add_log_listener(self, listener: LogCallback) -> None:
        self._log_listeners.append(listener)

    def remove_log_listener(self, listener: LogCallback) -> None:
        try:
            self._log_listeners.remove(listener)
        except ValueError:
            pass

    async def start(self) -> bool:
        async with self._lock:
            if self.is_running:
                return True

            if not self._binary_path.exists():
                self._log(
                    f"Error: cli-proxy-api-plus binary not found at {self._binary_path}"
                )
                return False

            await self._kill_orphans()
            config_path = self._write_merged_config()
            self._log(f"Starting cli-proxy-api-plus on port {self.port}")
            try:
                self._process = await asyncio.create_subprocess_exec(
                    str(self._binary_path),
                    "-config",
                    str(config_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                    env=os.environ.copy(),
                )
            except OSError as err:
                self._log(f"Failed to start server: {err}")
                return False

            assert self._process.stdout is not None
            assert self._process.stderr is not None
            self._readers = [
                asyncio.create_task(
                    self._consume_stream(self._process.stdout, prefix=""),
                    name="backend-stdout",
                ),
                asyncio.create_task(
                    self._consume_stream(self._process.stderr, prefix="stderr: "),
                    name="backend-stderr",
                ),
            ]

        await asyncio.sleep(READINESS_CHECK_DELAY)
        if not self.is_running:
            self._log("Server exited before becoming ready")
            return False
        self._log(f"Server started (pid {self._process.pid})")  # type: ignore[union-attr]
        return True

    async def stop(self) -> None:
        async with self._lock:
            proc = self._process
            if proc is None:
                return

            self._log(f"Stopping server (pid {proc.pid})")
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=GRACEFUL_TERMINATION_TIMEOUT)
            except TimeoutError:
                self._log("Server did not exit after SIGTERM; sending SIGKILL")
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()

            for task in self._readers:
                task.cancel()
            for task in self._readers:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._readers.clear()
            self._process = None
            self._log("Server stopped")

    async def run_auth_command(self, command: AuthCommand) -> AuthOutcome:
        """Spawn ``cli-proxy-api-plus`` with an auth flag and return the outcome.

        Mirrors the Swift helper that replies with "\\n" after 12 s to unstick
        the Codex callback prompt, and "2\\n" after 20 s to select Google One
        during Gemini login.
        """
        if not self._binary_path.exists():
            return AuthOutcome(False, f"Binary not found at {self._binary_path}")

        config_path = self._write_merged_config()
        self._log(f"Starting auth flow: {command.value}")
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._binary_path),
                "--config",
                str(config_path),
                command.flag,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
        except OSError as err:
            return AuthOutcome(False, f"Failed to start auth process: {err}")

        feeder = asyncio.create_task(self._feed_auth_stdin(process, command))
        try:
            stdout, stderr = await process.communicate()
        finally:
            feeder.cancel()
            try:
                await feeder
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        if stdout_text:
            for line in stdout_text.splitlines():
                self._log(f"auth[{command.value}]: {line}")
        if stderr_text:
            for line in stderr_text.splitlines():
                self._log(f"auth[{command.value}] stderr: {line}")

        combined = stdout_text + "\n" + stderr_text
        if process.returncode == 0 or "Opening browser" in combined or "Attempting to open URL" in combined:
            message = (
                "Browser opened for authentication. Complete the login in your "
                "browser; the app detects new credentials automatically."
            )
            return AuthOutcome(True, message)
        return AuthOutcome(
            False,
            stderr_text.strip()
            or stdout_text.strip()
            or f"Auth process exited with code {process.returncode}",
        )

    def get_merged_config_path(self) -> Path:
        return self._write_merged_config()

    # --- internals ----------------------------------------------------------

    def _write_merged_config(self) -> Path:
        template = bundled_config_yaml().read_text(encoding="utf-8")
        prefs = self._prefs_store.snapshot()
        template = template.replace(
            "  allow-remote: false",
            f"  allow-remote: {'true' if prefs.allow_remote else 'false'}",
        )
        template = template.replace(
            '  secret-key: ""  # Leave empty to disable management API',
            f'  secret-key: "{prefs.secret_key}"',
        )

        disabled = [
            self._PROVIDER_OAUTH_KEYS[k]
            for k, enabled in prefs.enabled_providers.items()
            if not enabled and k in self._PROVIDER_OAUTH_KEYS
        ]
        if disabled:
            template += "\n# Provider exclusions (auto-added by DroidProxy)\n"
            template += "oauth-excluded-models:\n"
            for provider in sorted(disabled):
                template += f"  {provider}:\n"
                template += '    - "*"\n'

        destination = merged_config_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = destination.with_suffix(destination.suffix + ".new")
        tmp_path.write_text(template, encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, destination)
        return destination

    async def _consume_stream(self, stream: asyncio.StreamReader, *, prefix: str) -> None:
        while True:
            try:
                line = await stream.readline()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                log.debug("Error reading backend stream: %s", err)
                return
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            self._log(f"{prefix}{text}")

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self._log_buffer.append(entry)
        log.info(message)
        snapshot = list(self._log_buffer)
        for listener in list(self._log_listeners):
            try:
                result = listener(snapshot)
            except Exception:
                log.debug("log listener raised", exc_info=True)
                continue
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(self._swallow(result))

    @staticmethod
    async def _swallow(coro: Any) -> None:
        try:
            await coro
        except Exception:
            log.debug("async log listener raised", exc_info=True)

    async def _feed_auth_stdin(
        self, process: asyncio.subprocess.Process, command: AuthCommand
    ) -> None:
        """Mirror the Swift timers that nudge the login subprocess stdin."""
        if process.stdin is None:
            return
        try:
            if command is AuthCommand.CODEX:
                await asyncio.sleep(12)
                if process.returncode is None:
                    process.stdin.write(b"\n")
                    await process.stdin.drain()
            elif command is AuthCommand.GEMINI:
                await asyncio.sleep(20)
                if process.returncode is None:
                    process.stdin.write(b"2\n")
                    await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return
        except asyncio.CancelledError:
            raise

    async def _kill_orphans(self) -> None:
        """Terminate any ``cli-proxy-api-plus`` instances from previous crashes."""

        def _iter_orphans() -> Iterable[psutil.Process]:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    name = proc.info.get("name") or ""
                    cmdline = proc.info.get("cmdline") or []
                    combined = f"{name} {' '.join(cmdline)}"
                    if "cli-proxy-api-plus" in combined:
                        yield proc
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        orphans = list(_iter_orphans())
        if not orphans:
            return
        self._log(f"Cleaning up {len(orphans)} orphaned process(es)")
        for proc in orphans:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        gone, alive = psutil.wait_procs(orphans, timeout=2.0)
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self._log("Orphan cleanup complete")


def kill_by_pid(pid: int, *, timeout: float = 2.0) -> None:
    """Utility: best-effort kill of a single PID."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc = psutil.Process(pid)
        proc.wait(timeout=timeout)
    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
