"""XDG-compliant filesystem paths used by DroidProxy.

The upstream ``cli-proxy-api-plus`` binary stores OAuth credentials under
``~/.cli-proxy-api`` on every platform, so we keep that path as-is to stay
compatible. Everything else lives under the standard XDG base directories.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "droidproxy"

_dirs = PlatformDirs(appname=APP_NAME, appauthor=False, roaming=False)


def config_dir() -> Path:
    """Directory for user configuration (``$XDG_CONFIG_HOME/droidproxy``)."""
    path = Path(_dirs.user_config_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    """Directory for user data (``$XDG_DATA_HOME/droidproxy``)."""
    path = Path(_dirs.user_data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    """Directory for user state / logs (``$XDG_STATE_HOME/droidproxy``)."""
    path = Path(_dirs.user_state_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    """Directory for user cache (``$XDG_CACHE_HOME/droidproxy``)."""
    path = Path(_dirs.user_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def binary_dir() -> Path:
    """Where the ``cli-proxy-api-plus`` binary is installed."""
    path = data_dir() / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cli_proxy_api_binary() -> Path:
    """Path to the bundled ``cli-proxy-api-plus`` binary."""
    return binary_dir() / "cli-proxy-api-plus"


def auth_dir() -> Path:
    """OAuth token directory. Must stay at ``~/.cli-proxy-api`` for compatibility
    with the upstream Go binary, which hardcodes this location.
    """
    path = Path.home() / ".cli-proxy-api"
    path.mkdir(parents=True, exist_ok=True)
    return path


def merged_config_path() -> Path:
    """Generated config file consumed by the Go subprocess."""
    return auth_dir() / "merged-config.yaml"


def prefs_path() -> Path:
    """User preferences TOML file."""
    return config_dir() / "config.toml"


def log_file() -> Path:
    """Rotating application log."""
    return state_dir() / "droidproxy.log"


def debug_log_file() -> Path:
    """Parity log path matching the macOS ``ThinkingProxy.fileLog`` location.

    The Swift app writes surgical proxy transformations to
    ``/tmp/droidproxy-debug.log``; we mirror the same path so users can diff
    behaviour between the macOS and Linux ports.
    """
    return Path("/tmp/droidproxy-debug.log")


def resources_dir() -> Path:
    """Bundled resources inside the installed package."""
    return Path(__file__).resolve().parent / "resources"


def icon_path(name: str) -> Path:
    """Absolute path to a bundled PNG icon."""
    return resources_dir() / name


def bundled_config_yaml() -> Path:
    """Template ``config.yaml`` distributed with the package."""
    return resources_dir() / "config.yaml"


def factory_droids_dir() -> Path:
    """Bundled Challenger Droid markdown files."""
    return resources_dir() / "factory" / "droids"


def factory_commands_dir() -> Path:
    """Bundled Challenger Droid slash-command markdown files."""
    return resources_dir() / "factory" / "commands"


def web_assets_dir() -> Path:
    """Static assets for the settings web UI."""
    return resources_dir() / "web"
