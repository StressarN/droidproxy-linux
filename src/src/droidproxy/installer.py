"""Install bundled Challenger Droid configs and Factory custom models.

Two public helpers:

* :func:`install_challenger_droids` copies the bundled ``.md`` files into
  ``~/.factory/droids/`` and ``~/.factory/commands/`` -- matches the shell
  snippet in the README.
* :func:`install_factory_custom_models` merges the DroidProxy model entries
  into ``~/.factory/settings.json`` -- mirrors the macOS ``Apply`` button
  in ``SettingsView.applyFactoryCustomModels()``. It removes any stale
  DroidProxy entries (including legacy IDs like ``opus-4-6`` and the
  ``custom:CC:*`` namespace) before re-appending the current set.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from droidproxy.paths import factory_commands_dir, factory_droids_dir

DROID_PROXY_MODELS: list[dict[str, Any]] = [
    {
        "model": "claude-opus-4-7",
        "id": "custom:droidproxy:opus-4-7",
        "baseUrl": "http://localhost:8317",
        "apiKey": "dummy-not-used",
        "displayName": "DroidProxy: Opus 4.7",
        "maxOutputTokens": 128000,
        "noImageSupport": False,
        "provider": "anthropic",
    },
    {
        "model": "claude-sonnet-4-6",
        "id": "custom:droidproxy:sonnet-4-6",
        "baseUrl": "http://localhost:8317",
        "apiKey": "dummy-not-used",
        "displayName": "DroidProxy: Sonnet 4.6",
        "maxOutputTokens": 64000,
        "noImageSupport": False,
        "provider": "anthropic",
    },
    {
        "model": "gpt-5.3-codex",
        "id": "custom:droidproxy:gpt-5.3-codex",
        "baseUrl": "http://localhost:8317/v1",
        "apiKey": "dummy-not-used",
        "displayName": "DroidProxy: GPT 5.3 Codex",
        "maxOutputTokens": 128000,
        "noImageSupport": False,
        "provider": "openai",
    },
    {
        "model": "gpt-5.4",
        "id": "custom:droidproxy:gpt-5.4",
        "baseUrl": "http://localhost:8317/v1",
        "apiKey": "dummy-not-used",
        "displayName": "DroidProxy: GPT 5.4",
        "maxOutputTokens": 128000,
        "noImageSupport": False,
        "provider": "openai",
    },
    {
        "model": "gemini-3.1-pro-preview",
        "id": "custom:droidproxy:gemini-3.1-pro",
        "baseUrl": "http://localhost:8317",
        "apiKey": "dummy-not-used",
        "displayName": "DroidProxy: Gemini 3.1 Pro",
        "maxOutputTokens": 65536,
        "noImageSupport": False,
        "provider": "google",
    },
    {
        "model": "gemini-3-flash-preview",
        "id": "custom:droidproxy:gemini-3-flash",
        "baseUrl": "http://localhost:8317",
        "apiKey": "dummy-not-used",
        "displayName": "DroidProxy: Gemini 3 Flash",
        "maxOutputTokens": 65536,
        "noImageSupport": False,
        "provider": "google",
    },
]

# IDs from previous DroidProxy releases that should be scrubbed during Apply
# so users don't end up with stale entries (e.g. Opus 4.6) next to the current
# ones. Matches ``legacyDroidProxyModelIds`` in the Swift app.
LEGACY_DROIDPROXY_MODEL_IDS: frozenset[str] = frozenset({"custom:droidproxy:opus-4-6"})


def install_challenger_droids(target_home: Path | None = None) -> dict[str, list[str]]:
    """Copy bundled droid markdown files into the user's Factory config.

    Returns a dict summarising the files installed for each category.
    """
    home = target_home or Path.home()
    droids_target = home / ".factory" / "droids"
    commands_target = home / ".factory" / "commands"
    droids_target.mkdir(parents=True, exist_ok=True)
    commands_target.mkdir(parents=True, exist_ok=True)

    installed_droids: list[str] = []
    installed_commands: list[str] = []

    for source in sorted(factory_droids_dir().glob("*.md")):
        dest = droids_target / source.name
        shutil.copyfile(source, dest)
        installed_droids.append(source.name)

    for source in sorted(factory_commands_dir().glob("*.md")):
        dest = commands_target / source.name
        shutil.copyfile(source, dest)
        installed_commands.append(source.name)

    return {
        "droids": installed_droids,
        "commands": installed_commands,
        "droids_target": [str(droids_target)],
        "commands_target": [str(commands_target)],
    }


def _provider_key_for(model: dict[str, Any]) -> str | None:
    """Return the DroidProxy provider key (``claude``/``codex``/``gemini``) for a model entry."""
    name = model.get("model")
    if not isinstance(name, str):
        return None
    if name.startswith("claude"):
        return "claude"
    if name.startswith("gpt"):
        return "codex"
    if name.startswith("gemini"):
        return "gemini"
    return None


def factory_settings_path(target_home: Path | None = None) -> Path:
    home = target_home or Path.home()
    return home / ".factory" / "settings.json"


def _enabled_model_ids(enabled_providers: dict[str, bool]) -> set[str]:
    result: set[str] = set()
    for model in DROID_PROXY_MODELS:
        key = _provider_key_for(model)
        if key is not None and not enabled_providers.get(key, True):
            continue
        mid = model.get("id")
        if isinstance(mid, str):
            result.add(mid)
    return result


def factory_custom_models_installed(
    enabled_providers: dict[str, bool],
    *,
    target_home: Path | None = None,
) -> bool:
    """True when every currently enabled DroidProxy model ID is already in ``settings.json``."""
    path = factory_settings_path(target_home)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    models = payload.get("customModels")
    if not isinstance(models, list):
        return False
    existing = {m.get("id") for m in models if isinstance(m, dict)}
    expected = _enabled_model_ids(enabled_providers)
    return bool(expected) and expected.issubset(existing)


def install_factory_custom_models(
    enabled_providers: dict[str, bool] | None = None,
    *,
    target_home: Path | None = None,
) -> dict[str, Any]:
    """Merge DroidProxy models into ``~/.factory/settings.json`` atomically.

    * Preserves any unrelated keys in the file.
    * Strips prior DroidProxy entries (current IDs, legacy IDs, and the
      ``custom:CC:*`` namespace) before re-appending.
    * Skips models whose provider has been disabled in the DroidProxy UI.
    * Creates ``~/.factory/`` when missing.
    """
    providers = {"claude": True, "codex": True, "gemini": True}
    if enabled_providers:
        providers.update(enabled_providers)

    path = factory_settings_path(target_home)
    path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings = loaded
        except (OSError, ValueError):
            settings = {}

    models = settings.get("customModels")
    if not isinstance(models, list):
        models = []

    current_ids = {m["id"] for m in DROID_PROXY_MODELS}
    cleaned: list[dict[str, Any]] = []
    removed_ids: list[str] = []
    for entry in models:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id")
        if isinstance(eid, str) and (
            eid in current_ids
            or eid in LEGACY_DROIDPROXY_MODEL_IDS
            or eid.startswith("custom:CC:")
        ):
            removed_ids.append(eid)
            continue
        cleaned.append(entry)

    enabled_models = [
        dict(model)
        for model in DROID_PROXY_MODELS
        if (_provider_key_for(model) is None)
        or providers.get(_provider_key_for(model), True)
    ]

    start_index = len(cleaned)
    for offset, model in enumerate(enabled_models):
        model["index"] = start_index + offset
        cleaned.append(model)

    settings["customModels"] = cleaned

    serialised = json.dumps(settings, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".factory-settings-", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(serialised)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    installed_ids = [m["id"] for m in enabled_models]
    skipped_ids = [
        m["id"]
        for m in DROID_PROXY_MODELS
        if m["id"] not in installed_ids
    ]
    return {
        "installed": installed_ids,
        "removed": removed_ids,
        "skipped": skipped_ids,
        "settings_path": str(path),
        "total_models": len(cleaned),
    }
