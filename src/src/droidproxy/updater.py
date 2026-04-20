"""Check for DroidProxy Linux releases and hand off to the right upgrader.

Unlike macOS we don't have Sparkle. Instead we detect the install method
and tell the user how to upgrade, or launch ``AppImageUpdate`` directly if
we're inside an AppImage.

Install method detection:
* ``APPIMAGE`` env var present -> AppImage
* Parent directory of ``sys.executable`` contains ``pipx`` -> pipx
* ``/usr/bin/droidproxy`` exists and is a regular file -> distro package
* otherwise -> source / venv
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.request import Request, urlopen

from droidproxy import __version__
from droidproxy.paths import cache_dir

log = logging.getLogger(__name__)

LATEST_RELEASE_URL = "https://api.github.com/repos/anand-92/droidproxy/releases/latest"
DEFAULT_USER_AGENT = f"droidproxy-linux/{__version__}"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60


class InstallMethod(StrEnum):
    APPIMAGE = "appimage"
    PIPX = "pipx"
    DISTRO = "distro"
    SOURCE = "source"


@dataclass(frozen=True)
class UpdateInfo:
    installed: str
    latest: str | None
    newer_available: bool
    install_method: InstallMethod
    upgrade_hint: str
    html_url: str | None = None


def detect_install_method() -> InstallMethod:
    """Best-effort detection of how this process was installed."""
    if os.environ.get("APPIMAGE"):
        return InstallMethod.APPIMAGE
    executable = Path(sys.executable).resolve()
    if "pipx" in executable.parts:
        return InstallMethod.PIPX
    if Path("/usr/bin/droidproxy").is_file():
        return InstallMethod.DISTRO
    return InstallMethod.SOURCE


def _upgrade_hint_for(method: InstallMethod) -> str:
    return {
        InstallMethod.APPIMAGE: (
            "Use AppImageUpdate against the .zsync file next to the AppImage, "
            "or download the latest AppImage from the releases page."
        ),
        InstallMethod.PIPX: "pipx upgrade droidproxy",
        InstallMethod.DISTRO: (
            "Your distro package manager handles updates "
            "(e.g. `paru -Syu droidproxy` or `sudo pacman -Syu`)."
        ),
        InstallMethod.SOURCE: (
            "Run `git pull && pip install -e linux` in the checkout."
        ),
    }[method]


def _parse_version(value: str) -> tuple[int, ...]:
    cleaned = value.lstrip("v")
    parts: list[int] = []
    for chunk in cleaned.replace("-", ".").split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class Updater:
    """Polls the GitHub releases API once a day, caching results on disk."""

    def __init__(
        self,
        *,
        current_version: str = __version__,
        release_url: str = LATEST_RELEASE_URL,
        cache_file: Path | None = None,
    ) -> None:
        self._current = current_version
        self._release_url = release_url
        self._cache = cache_file or (cache_dir() / "update_check.json")
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop(), name="updater-poll")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def check_for_updates(self, *, interactive: bool = False) -> UpdateInfo:
        info = await asyncio.to_thread(self._fetch_update_info_blocking)
        if interactive:
            await asyncio.to_thread(self._dispatch_interactive, info)
        self._write_cache(info)
        return info

    async def _poll_loop(self) -> None:
        try:
            while True:
                try:
                    await self.check_for_updates()
                except Exception:
                    log.debug("Update poll failed", exc_info=True)
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise

    def _fetch_update_info_blocking(self) -> UpdateInfo:
        method = detect_install_method()
        try:
            request = Request(self._release_url, headers={"User-Agent": DEFAULT_USER_AGENT})
            with urlopen(request, timeout=20) as resp:
                payload = json.load(resp)
        except Exception as err:
            log.debug("Could not fetch release metadata: %s", err)
            return UpdateInfo(
                installed=self._current,
                latest=None,
                newer_available=False,
                install_method=method,
                upgrade_hint=_upgrade_hint_for(method),
            )
        latest = payload.get("tag_name") or payload.get("name")
        html_url = payload.get("html_url")
        if not isinstance(latest, str):
            return UpdateInfo(
                installed=self._current,
                latest=None,
                newer_available=False,
                install_method=method,
                upgrade_hint=_upgrade_hint_for(method),
                html_url=html_url,
            )
        newer = _parse_version(latest) > _parse_version(self._current)
        return UpdateInfo(
            installed=self._current,
            latest=latest,
            newer_available=newer,
            install_method=method,
            upgrade_hint=_upgrade_hint_for(method),
            html_url=html_url,
        )

    def _dispatch_interactive(self, info: UpdateInfo) -> None:
        """Attempt to launch the platform-specific updater.

        Only ``AppImageUpdate`` is actually launched; for pipx/distro/source we
        rely on the caller (tray or CLI) to render the hint to the user.
        """
        if not info.newer_available:
            return
        if info.install_method is InstallMethod.APPIMAGE:
            appimage = os.environ.get("APPIMAGE")
            updater = shutil.which("AppImageUpdate") or shutil.which("appimageupdatetool")
            if appimage and updater:
                try:
                    subprocess.Popen([updater, appimage], close_fds=True)
                except OSError as err:
                    log.warning("Failed to launch AppImageUpdate: %s", err)
            return

    def _write_cache(self, info: UpdateInfo) -> None:
        payload = {
            "checked_at": int(time.time()),
            "installed": info.installed,
            "latest": info.latest,
            "newer_available": info.newer_available,
            "install_method": info.install_method.value,
        }
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as err:
            log.debug("Failed to write update cache: %s", err)


async def check_and_print(current_version: str = __version__) -> int:
    """CLI helper used by ``droidproxy check-update``."""
    updater = Updater(current_version=current_version)
    info = await updater.check_for_updates(interactive=False)
    if info.latest is None:
        print(f"Could not contact the releases API. You are on {info.installed}.")
        return 1
    if info.newer_available:
        print(
            f"DroidProxy {info.latest} is available "
            f"(you have {info.installed}).\n{info.upgrade_hint}"
        )
        return 0
    print(f"DroidProxy {info.installed} is up to date.")
    return 0
