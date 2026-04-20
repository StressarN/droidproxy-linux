"""Expose the proxy via ``cloudflared tunnel --url``.

Port of ``src/Sources/TunnelManager.swift``. Keeps the same behaviour:

* Search common install paths and ``$PATH`` for the binary.
* Spawn ``cloudflared tunnel --url http://localhost:<port>``.
* Parse the first ``https://<...>.trycloudflare.com`` URL from stderr/stdout
  and expose it via :attr:`TunnelManager.public_url`.
* Time out after ``URL_TIMEOUT`` if no URL was printed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

_COMMON_PATHS = ("/usr/bin/cloudflared", "/usr/local/bin/cloudflared", "/opt/cloudflared")
_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

URL_TIMEOUT = 10.0


def find_cloudflared() -> Path | None:
    """Locate the ``cloudflared`` binary, returning ``None`` if missing."""
    for candidate in _COMMON_PATHS:
        path = Path(candidate)
        if path.exists() and path.is_file():
            return path
    resolved = shutil.which("cloudflared")
    return Path(resolved) if resolved else None


class CloudflaredNotInstalled(RuntimeError):
    """Raised when cloudflared is required but not found on the host."""


class TunnelManager:
    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._readers: list[asyncio.Task[None]] = []
        self.public_url: str | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self, port: int) -> dict[str, object]:
        if self.is_running:
            return {"running": True, "url": self.public_url}

        binary = find_cloudflared()
        if binary is None:
            return {
                "running": False,
                "url": None,
                "error": "cloudflared is not installed",
                "install_hint": (
                    "Install cloudflared via your package manager "
                    "(e.g. `sudo pacman -S cloudflared` on Arch) or download "
                    "from https://github.com/cloudflare/cloudflared/releases"
                ),
            }

        self._process = await asyncio.create_subprocess_exec(
            str(binary),
            "tunnel",
            "--url",
            f"http://localhost:{port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        ready = asyncio.Event()

        async def consume(stream: asyncio.StreamReader | None) -> None:
            if stream is None:
                return
            while True:
                try:
                    chunk = await stream.readline()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return
                if not chunk:
                    return
                text = chunk.decode("utf-8", errors="replace")
                match = _URL_RE.search(text)
                if match and not ready.is_set():
                    self.public_url = match.group(0)
                    ready.set()

        self._readers = [
            asyncio.create_task(consume(self._process.stdout), name="tunnel-stdout"),
            asyncio.create_task(consume(self._process.stderr), name="tunnel-stderr"),
        ]

        try:
            await asyncio.wait_for(ready.wait(), timeout=URL_TIMEOUT)
        except TimeoutError:
            await self.stop()
            return {
                "running": False,
                "url": None,
                "error": "Timed out waiting for a trycloudflare.com URL",
            }

        return {"running": True, "url": self.public_url}

    async def stop(self) -> None:
        proc = self._process
        self._process = None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
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
        self.public_url = None
