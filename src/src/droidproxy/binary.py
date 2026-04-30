"""Download, verify, and manage the upstream ``cli-proxy-api`` binary.

Upstream publishes release tarballs at
``https://github.com/router-for-me/CLIProxyAPI/releases/`` with names like
``CLIProxyAPI_<version>_linux_amd64.tar.gz`` and a ``checksums.txt``. We
pin the version in this module so builds are reproducible; the
``update-cliproxyapi-linux.yml`` workflow bumps that pin automatically.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from droidproxy.paths import binary_dir, cli_proxy_api_binary

log = logging.getLogger(__name__)

PINNED_VERSION = "6.9.43"
GITHUB_REPO = "router-for-me/CLIProxyAPI"
RELEASE_URL_TEMPLATE = (
    "https://github.com/router-for-me/CLIProxyAPI/releases/download/v{version}/{asset}"
)
USER_AGENT = "droidproxy-linux/1.0 (+https://github.com/StressarN/droidproxy-linux)"

_ARCH_MAP = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}

_CANDIDATE_NAMES = (
    "CLIProxyAPI",
    "cli-proxy-api",
    "CLIProxyAPIPlus",
    "cli-proxy-api-plus",
)


class BinaryError(RuntimeError):
    """Raised when the binary cannot be installed or verified."""


@dataclass(frozen=True)
class BinaryStatus:
    path: Path
    exists: bool
    version: str
    size: int


def detect_arch() -> str:
    """Return the Go-style arch name for the current host."""
    raw = platform.machine().lower()
    try:
        return _ARCH_MAP[raw]
    except KeyError as err:
        raise BinaryError(
            f"Unsupported architecture {raw!r}; supported: {sorted(set(_ARCH_MAP.values()))}"
        ) from err


def asset_name(version: str = PINNED_VERSION, arch: str | None = None) -> str:
    arch = arch or detect_arch()
    return f"CLIProxyAPI_{version}_linux_{arch}.tar.gz"


def release_url(version: str = PINNED_VERSION, arch: str | None = None) -> str:
    return RELEASE_URL_TEMPLATE.format(version=version, asset=asset_name(version, arch))


def checksums_url(version: str = PINNED_VERSION) -> str:
    return RELEASE_URL_TEMPLATE.format(version=version, asset="checksums.txt")


def current_status() -> BinaryStatus:
    path = cli_proxy_api_binary()
    exists = path.exists()
    size = path.stat().st_size if exists else 0
    return BinaryStatus(path=path, exists=exists, version=PINNED_VERSION, size=size)


def _http_get(url: str) -> bytes:
    """Fetch a URL and return its bytes. Raises :class:`BinaryError` on failure."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=60) as resp:
            return resp.read()
    except Exception as err:
        raise BinaryError(f"Failed to download {url}: {err}") from err


def _parse_checksums(text: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, name = parts
        name = name.lstrip("*")
        checksums[name] = digest.lower()
    return checksums


def verify_sha256(data: bytes, expected_hex: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != expected_hex.lower():
        raise BinaryError(
            f"Checksum mismatch: expected {expected_hex}, got {actual}"
        )


def _extract_binary(tarball: bytes, destination: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="droidproxy-bin-") as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / "archive.tar.gz"
        archive_path.write_bytes(tarball)
        with tarfile.open(archive_path, mode="r:gz") as tar:
            # Pick the first member that looks like an executable (no '/', no
            # extension and present in the CANDIDATE_NAMES set first).
            members = tar.getmembers()
            candidates = [m for m in members if m.isfile()]
            picked = None
            for want in _CANDIDATE_NAMES:
                for member in candidates:
                    name = os.path.basename(member.name)
                    if name == want:
                        picked = member
                        break
                if picked is not None:
                    break
            if picked is None:
                for member in candidates:
                    name = os.path.basename(member.name)
                    if (
                        member.size > 1_000_000
                        and "." not in name
                        and "/" not in member.name.lstrip("./")
                    ):
                        picked = member
                        break
            if picked is None:
                raise BinaryError(
                    "Could not locate the cli-proxy-api binary inside the release tarball"
                )
            tar.extract(picked, path=tmp_path, filter="data")
            extracted = tmp_path / picked.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = destination.with_suffix(destination.suffix + ".new")
        tmp_target.write_bytes(extracted.read_bytes())
        os.chmod(tmp_target, 0o755)
        os.replace(tmp_target, destination)
    return destination


def install(
    version: str = PINNED_VERSION,
    *,
    force: bool = False,
    arch: str | None = None,
) -> BinaryStatus:
    """Download and install the pinned upstream binary.

    Skips the download when a binary is already present and ``force`` is
    False. Always verifies the tarball against the upstream checksums file.
    """
    target = cli_proxy_api_binary()
    if target.exists() and not force:
        log.info("cli-proxy-api already installed at %s", target)
        return current_status()

    binary_dir()  # ensure directory exists
    asset = asset_name(version, arch)
    url = release_url(version, arch)
    log.info("Downloading %s", url)

    tarball = _http_get(url)

    checks_text = _http_get(checksums_url(version)).decode("utf-8", errors="replace")
    checksums = _parse_checksums(checks_text)
    if asset not in checksums:
        raise BinaryError(f"Asset {asset} missing from upstream checksums.txt")
    verify_sha256(tarball, checksums[asset])

    _extract_binary(tarball, target)
    log.info("Installed cli-proxy-api %s at %s", version, target)
    return current_status()


def ensure_installed() -> Path:
    """Ensure the binary is present, installing it if necessary."""
    status = current_status()
    if status.exists and status.size > 1_000_000:
        return status.path
    install()
    return cli_proxy_api_binary()
