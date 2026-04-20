"""User preferences backed by a TOML file.

Mirrors the keys and defaults from the macOS ``AppPreferences.swift``.
Each accessor goes through :class:`Preferences` so the in-memory state stays
consistent with the file on disk. Writes are atomic (``tempfile`` + rename).
"""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from threading import RLock
from typing import Any

import tomli_w

from droidproxy.paths import prefs_path

log = logging.getLogger(__name__)


OPUS_47_EFFORTS = ("low", "medium", "high", "xhigh", "max")
SONNET_46_EFFORTS = ("low", "medium", "high", "max")
GPT_53_CODEX_EFFORTS = ("low", "medium", "high", "xhigh")
GPT_54_EFFORTS = ("low", "medium", "high", "xhigh")
GEMINI_31_PRO_LEVELS = ("low", "medium", "high")
GEMINI_3_FLASH_LEVELS = ("minimal", "low", "medium", "high")


@dataclass
class Preferences:
    opus47_thinking_effort: str = "xhigh"
    sonnet46_thinking_effort: str = "high"
    gpt53_codex_reasoning_effort: str = "high"
    gpt54_reasoning_effort: str = "high"
    gpt53_codex_fast_mode: bool = False
    gpt54_fast_mode: bool = False
    gemini31_pro_thinking_level: str = "high"
    gemini3_flash_thinking_level: str = "high"
    claude_max_budget_mode: bool = False
    allow_remote: bool = False
    secret_key: str = ""
    oled_theme: bool = False
    enabled_providers: dict[str, bool] = field(
        default_factory=lambda: {"claude": True, "codex": True, "gemini": True}
    )


_VALID = {
    "opus47_thinking_effort": OPUS_47_EFFORTS,
    "sonnet46_thinking_effort": SONNET_46_EFFORTS,
    "gpt53_codex_reasoning_effort": GPT_53_CODEX_EFFORTS,
    "gpt54_reasoning_effort": GPT_54_EFFORTS,
    "gemini31_pro_thinking_level": GEMINI_31_PRO_LEVELS,
    "gemini3_flash_thinking_level": GEMINI_3_FLASH_LEVELS,
}


def _coerce(value: Any, default: Any) -> Any:
    """Best-effort coercion of a loaded TOML value to the type of ``default``."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(default, dict):
        return dict(value) if isinstance(value, dict) else default
    if isinstance(default, str):
        return str(value)
    return value


class PreferencesStore:
    """Thread-safe view of the on-disk preferences file."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or prefs_path()
        self._lock = RLock()
        self._prefs = Preferences()
        self.reload()

    @property
    def path(self) -> Path:
        return self._path

    def reload(self) -> None:
        """Load preferences from disk. Missing file is treated as defaults."""
        with self._lock:
            if not self._path.exists():
                self._prefs = Preferences()
                return
            try:
                with self._path.open("rb") as fh:
                    data = tomllib.load(fh)
            except (tomllib.TOMLDecodeError, OSError) as err:
                log.warning("Failed to read %s (%s), resetting to defaults.", self._path, err)
                self._prefs = Preferences()
                return
            merged = asdict(Preferences())
            for key, default in list(merged.items()):
                if key in data:
                    coerced = _coerce(data[key], default)
                    if key in _VALID and coerced not in _VALID[key]:
                        log.warning(
                            "Ignoring invalid value %r for %s; keeping default %r.",
                            coerced,
                            key,
                            default,
                        )
                        continue
                    merged[key] = coerced
            self._prefs = Preferences(**merged)

    def snapshot(self) -> Preferences:
        with self._lock:
            return Preferences(**asdict(self._prefs))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self.snapshot())

    def get(self, key: str) -> Any:
        with self._lock:
            return getattr(self._prefs, key)

    def set(self, key: str, value: Any) -> Any:
        """Update a single preference with validation and persist it."""
        with self._lock:
            if not any(f.name == key for f in fields(Preferences)):
                raise KeyError(f"Unknown preference: {key}")
            default = getattr(Preferences(), key)
            coerced = _coerce(value, default)
            if key in _VALID and coerced not in _VALID[key]:
                raise ValueError(
                    f"Invalid value {coerced!r} for {key}; expected one of {_VALID[key]}."
                )
            setattr(self._prefs, key, coerced)
            self._persist_locked()
            return coerced

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Batch-update preferences and persist once at the end."""
        applied: dict[str, Any] = {}
        with self._lock:
            for key, value in changes.items():
                applied[key] = self._apply_locked(key, value)
            self._persist_locked()
        return applied

    def set_provider_enabled(self, provider: str, enabled: bool) -> bool:
        provider = provider.lower()
        if provider not in {"claude", "codex", "gemini"}:
            raise ValueError(f"Unknown provider: {provider}")
        with self._lock:
            providers = dict(self._prefs.enabled_providers)
            providers[provider] = bool(enabled)
            self._prefs.enabled_providers = providers
            self._persist_locked()
            return providers[provider]

    def is_provider_enabled(self, provider: str) -> bool:
        with self._lock:
            return bool(self._prefs.enabled_providers.get(provider.lower(), True))

    def disabled_providers(self) -> list[str]:
        """Provider IDs that the user has disabled (matches oauth-excluded keys)."""
        mapping = {"claude": "claude", "codex": "codex", "gemini": "gemini-cli"}
        with self._lock:
            return sorted(
                mapping[k]
                for k, v in self._prefs.enabled_providers.items()
                if not v and k in mapping
            )

    def _apply_locked(self, key: str, value: Any) -> Any:
        if not any(f.name == key for f in fields(Preferences)):
            raise KeyError(f"Unknown preference: {key}")
        default = getattr(Preferences(), key)
        coerced = _coerce(value, default)
        if key in _VALID and coerced not in _VALID[key]:
            raise ValueError(
                f"Invalid value {coerced!r} for {key}; expected one of {_VALID[key]}."
            )
        setattr(self._prefs, key, coerced)
        return coerced

    def _persist_locked(self) -> None:
        payload = asdict(self._prefs)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".droidproxy-", dir=str(self._path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                tomli_w.dump(payload, fh)
            os.replace(tmp_path, self._path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


_default_store: PreferencesStore | None = None


def get_store() -> PreferencesStore:
    """Return (and lazily create) the process-wide preferences store."""
    global _default_store
    if _default_store is None:
        _default_store = PreferencesStore()
    return _default_store


def reset_default_store_for_tests() -> None:
    """Test hook: drop the cached singleton so a new path can be used."""
    global _default_store
    _default_store = None
