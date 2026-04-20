"""Scan and manage OAuth credential files in ``~/.cli-proxy-api/``.

Port of the Swift ``AuthStatus.swift`` + ``AuthManager`` class. Exposes an
:class:`AuthManager` singleton view and a :class:`AuthWatcher` that wraps
``watchdog`` so changes get broadcast over the web UI SSE channel.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from droidproxy.paths import auth_dir

log = logging.getLogger(__name__)


class ServiceType(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"

    @property
    def display_name(self) -> str:
        return {
            ServiceType.CLAUDE: "Claude Code",
            ServiceType.CODEX: "Codex",
            ServiceType.GEMINI: "Gemini",
        }[self]

    @classmethod
    def from_raw(cls, value: str) -> ServiceType | None:
        try:
            return cls(value.lower())
        except ValueError:
            return None


_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
)


def _parse_iso8601(value: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class AuthAccount:
    id: str
    type: ServiceType
    file_path: Path
    email: str | None = None
    login: str | None = None
    expired: datetime | None = None
    disabled: bool = False

    @property
    def is_expired(self) -> bool:
        if self.expired is None:
            return False
        return self.expired < datetime.now(UTC)

    @property
    def display_name(self) -> str:
        if self.email:
            return self.email
        if self.login:
            return self.login
        return self.id

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.type.value,
            "display_name": self.display_name,
            "email": self.email,
            "login": self.login,
            "expired": self.expired.isoformat() if self.expired else None,
            "disabled": self.disabled,
            "is_expired": self.is_expired,
            "file_path": str(self.file_path),
        }


@dataclass
class ServiceAccounts:
    type: ServiceType
    accounts: list[AuthAccount] = field(default_factory=list)

    @property
    def has_accounts(self) -> bool:
        return bool(self.accounts)

    @property
    def active_count(self) -> int:
        return sum(1 for a in self.accounts if not a.is_expired and not a.disabled)


class AuthManager:
    """In-memory view of ``~/.cli-proxy-api/*.json``."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or auth_dir()
        self._lock = RLock()
        self._accounts: dict[ServiceType, ServiceAccounts] = {
            t: ServiceAccounts(type=t) for t in ServiceType
        }
        self.refresh()

    @property
    def directory(self) -> Path:
        return self._dir

    def accounts_for(self, type_: ServiceType) -> list[AuthAccount]:
        with self._lock:
            return list(self._accounts[type_].accounts)

    def all_accounts(self) -> list[AuthAccount]:
        with self._lock:
            return [a for sa in self._accounts.values() for a in sa.accounts]

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        with self._lock:
            return {
                type_.value: [a.to_dict() for a in self._accounts[type_].accounts]
                for type_ in ServiceType
            }

    def refresh(self) -> None:
        buckets: dict[ServiceType, list[AuthAccount]] = {t: [] for t in ServiceType}
        if not self._dir.exists():
            with self._lock:
                for t in ServiceType:
                    self._accounts[t] = ServiceAccounts(type=t, accounts=[])
            return

        for file in sorted(self._dir.iterdir()):
            if file.suffix != ".json" or not file.is_file():
                continue
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as err:
                log.debug("Skipping unreadable auth file %s: %s", file, err)
                continue
            if not isinstance(data, dict):
                continue
            type_str = data.get("type")
            if not isinstance(type_str, str):
                continue
            service = ServiceType.from_raw(type_str)
            if service is None:
                continue
            expired_raw = data.get("expired")
            expired = _parse_iso8601(expired_raw) if isinstance(expired_raw, str) else None
            account = AuthAccount(
                id=file.name,
                type=service,
                file_path=file,
                email=data.get("email") if isinstance(data.get("email"), str) else None,
                login=data.get("login") if isinstance(data.get("login"), str) else None,
                expired=expired,
                disabled=bool(data.get("disabled", False)),
            )
            buckets[service].append(account)

        with self._lock:
            for type_, accounts in buckets.items():
                self._accounts[type_] = ServiceAccounts(type=type_, accounts=accounts)

    def toggle_disabled(self, account_id: str) -> bool:
        """Mirror the Swift guard: refuse to disable the last enabled account."""
        with self._lock:
            for sa in self._accounts.values():
                for account in sa.accounts:
                    if account.id == account_id:
                        currently_disabled = account.disabled
                        if not currently_disabled:
                            enabled = sum(
                                1
                                for other in self._accounts[account.type].accounts
                                if not other.disabled
                            )
                            if enabled <= 1:
                                return False
                        self._write_disabled_flag(
                            account.file_path, not currently_disabled
                        )
                        self.refresh()
                        return True
        return False

    def delete_account(self, account_id: str) -> bool:
        with self._lock:
            for sa in self._accounts.values():
                for account in sa.accounts:
                    if account.id == account_id:
                        try:
                            account.file_path.unlink()
                        except OSError as err:
                            log.warning("Failed to delete %s: %s", account.file_path, err)
                            return False
                        self.refresh()
                        return True
        return False

    @staticmethod
    def _write_disabled_flag(path: Path, disabled: bool) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} does not contain a JSON object")
        data["disabled"] = disabled
        serialised = json.dumps(data, sort_keys=True, ensure_ascii=False)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(serialised, encoding="utf-8")
        tmp.replace(path)


AuthChangeCallback = Callable[[dict[str, list[dict[str, object]]]], Awaitable[None] | None]


class AuthWatcher:
    """Watch the auth directory and fan out changes to async listeners."""

    DEBOUNCE_SECONDS = 0.5

    def __init__(
        self,
        manager: AuthManager,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._manager = manager
        self._loop = loop
        self._observer: Observer | None = None
        self._listeners: list[AuthChangeCallback] = []
        self._pending: asyncio.TimerHandle | None = None
        self._lock = asyncio.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_event_loop()
        return self._loop

    def add_listener(self, listener: AuthChangeCallback) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: AuthChangeCallback) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def start(self) -> None:
        if self._observer is not None:
            return
        self._ensure_loop()
        self._manager.directory.mkdir(parents=True, exist_ok=True)
        handler = _Handler(self._on_fs_event)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._manager.directory), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=2.0)
        self._observer = None
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None

    def _on_fs_event(self, event: FileSystemEvent) -> None:
        """watchdog callback (runs on the observer thread).

        We debounce on the asyncio loop because the Go binary writes
        credential files in several steps.
        """
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._schedule_refresh)

    def _schedule_refresh(self) -> None:
        loop = self._loop
        if loop is None:
            return
        if self._pending is not None:
            self._pending.cancel()
        self._pending = loop.call_later(self.DEBOUNCE_SECONDS, self._dispatch_refresh)

    def _dispatch_refresh(self) -> None:
        self._pending = None
        loop = self._loop
        if loop is None:
            return
        asyncio.ensure_future(self._emit_refresh(), loop=loop)

    async def _emit_refresh(self) -> None:
        async with self._lock:
            self._manager.refresh()
            snapshot = self._manager.snapshot()
        for listener in list(self._listeners):
            try:
                result = listener(snapshot)
            except Exception:
                log.debug("auth listener raised", exc_info=True)
                continue
            if asyncio.iscoroutine(result):
                try:
                    await result
                except Exception:
                    log.debug("async auth listener raised", exc_info=True)


class _Handler(FileSystemEventHandler):
    """Adapts ``FileSystemEventHandler`` to a plain callback."""

    def __init__(self, callback: Callable[[FileSystemEvent], None]) -> None:
        self._callback = callback

    def on_any_event(self, event: FileSystemEvent) -> None:
        self._callback(event)
