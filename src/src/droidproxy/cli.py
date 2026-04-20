"""Command-line entry point for the ``droidproxy`` command."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys

from droidproxy import __version__
from droidproxy.app import AppOptions, run_daemon, run_with_tray
from droidproxy.paths import config_dir, data_dir, state_dir
from droidproxy.proxy import ProxyConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="droidproxy",
        description="Factory Droid proxy for Claude, Codex, and Gemini.",
    )
    parser.add_argument("--version", action="version", version=f"droidproxy {__version__}")
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING). Default: INFO.",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8316,
        help="Port for the settings web UI. Default: 8316.",
    )
    parser.add_argument(
        "--proxy-port",
        type=int,
        default=8317,
        help="Port for the thinking proxy. Default: 8317.",
    )
    parser.add_argument(
        "--upstream-port",
        type=int,
        default=8318,
        help="Port for the cli-proxy-api-plus backend. Default: 8318.",
    )
    parser.add_argument(
        "--no-auto-download",
        action="store_true",
        help="Do not download the cli-proxy-api-plus binary on first run.",
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("tray", help="Run the GTK tray (default when no subcommand given).")
    daemon_cmd = sub.add_parser(
        "daemon",
        help="Run headless, without the GTK tray.",
    )
    daemon_cmd.add_argument(
        "--detach",
        "-d",
        action="store_true",
        help=(
            "Double-fork into the background, redirect stdio to the log "
            "file, and write a pidfile. Manage with `droidproxy stop` / "
            "`droidproxy status`."
        ),
    )
    sub.add_parser(
        "stop", help="Send SIGTERM to the detached daemon and wait for it to exit."
    )
    sub.add_parser(
        "status", help="Show whether the detached daemon is running."
    )
    sub.add_parser("install-droids", help="Copy Challenger Droid configs to ~/.factory/.")
    sub.add_parser(
        "install-models",
        help="Apply DroidProxy custom models to ~/.factory/settings.json.",
    )
    sub.add_parser("install-binary", help="(Re)download cli-proxy-api-plus.")
    sub.add_parser("doctor", help="Run health checks and print diagnostics.")
    sub.add_parser("check-update", help="Check for a newer DroidProxy release.")
    sub.add_parser("paths", help="Print the XDG paths DroidProxy uses.")

    parser.set_defaults(command=None)
    return parser


def _configure_logging(level_name: str) -> None:
    numeric = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )


def _options_from_args(args: argparse.Namespace) -> AppOptions:
    return AppOptions(
        web_port=args.web_port,
        proxy_config=ProxyConfig(
            listen_port=args.proxy_port,
            upstream_port=args.upstream_port,
        ),
        auto_download_binary=not args.no_auto_download,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)

    command = args.command
    options = _options_from_args(args)

    if command in (None, "tray"):
        return _run_tray_or_fallback(options)
    if command == "daemon":
        if getattr(args, "detach", False):
            from droidproxy.app import daemonize

            # daemonize() is fatal-on-error: either we come back as the
            # grandchild (real process) or SystemExit(1) because another
            # daemon is already running.
            daemonize()
        return run_daemon(options)
    if command == "stop":
        from droidproxy.app import stop_daemon

        return stop_daemon()
    if command == "status":
        from droidproxy.app import daemon_status

        return daemon_status()
    if command == "install-droids":
        return _install_droids()
    if command == "install-models":
        return _install_models()
    if command == "install-binary":
        return _install_binary()
    if command == "doctor":
        return _doctor(options)
    if command == "check-update":
        from droidproxy.updater import check_and_print

        return asyncio.run(check_and_print())
    if command == "paths":
        print(f"config dir: {config_dir()}")
        print(f"data dir:   {data_dir()}")
        print(f"state dir:  {state_dir()}")
        return 0
    parser.error(f"Unknown command: {command}")
    return 2


def _run_tray_or_fallback(options: AppOptions) -> int:
    try:
        return run_with_tray(options)
    except Exception as err:  # noqa: BLE001
        # If GTK can't load (headless boxes, broken dependencies), keep the
        # service alive so users can still reach the web UI.
        print(
            f"[droidproxy] GTK tray unavailable ({err}); starting in daemon mode. "
            f"Use `droidproxy daemon` to skip this attempt.",
            file=sys.stderr,
        )
        return run_daemon(options)


def _install_droids() -> int:
    from droidproxy.installer import install_challenger_droids

    result = install_challenger_droids()
    droids = ", ".join(result["droids"]) or "(none)"
    commands = ", ".join(result["commands"]) or "(none)"
    print(f"Installed droids: {droids}")
    print(f"Installed commands: {commands}")
    return 0


def _install_models() -> int:
    from droidproxy.installer import install_factory_custom_models
    from droidproxy.prefs import get_store

    prefs = get_store().snapshot()
    try:
        result = install_factory_custom_models(prefs.enabled_providers)
    except OSError as err:
        print(f"Failed to apply Factory custom models: {err}", file=sys.stderr)
        return 1
    installed = ", ".join(result["installed"]) or "(none)"
    skipped = ", ".join(result["skipped"]) or "(none)"
    removed = ", ".join(result["removed"]) or "(none)"
    print(f"Wrote {result['settings_path']}")
    print(f"Applied: {installed}")
    print(f"Skipped (provider disabled): {skipped}")
    print(f"Removed stale entries: {removed}")
    print(
        "Restart Factory (or open a new session) to see the new models in the picker."
    )
    return 0


def _install_binary() -> int:
    from droidproxy import binary

    try:
        status = binary.install(force=True)
    except binary.BinaryError as err:
        print(f"Failed to install cli-proxy-api-plus: {err}", file=sys.stderr)
        return 1
    print(f"Installed cli-proxy-api-plus {status.version} ({status.size} bytes) at {status.path}")
    return 0


def _doctor(options: AppOptions) -> int:
    from droidproxy import binary
    from droidproxy.tunnel import find_cloudflared

    print(f"droidproxy {__version__}")
    print(f"config dir: {config_dir()}")
    print(f"data dir:   {data_dir()}")
    print(f"state dir:  {state_dir()}")

    status = binary.current_status()
    if status.exists:
        print(f"cli-proxy-api-plus: OK ({status.size} bytes at {status.path})")
    else:
        print(f"cli-proxy-api-plus: MISSING ({status.path})")

    cf = find_cloudflared()
    print(f"cloudflared:        {'OK (' + str(cf) + ')' if cf else 'MISSING (tunnel disabled)'}")

    xdg_open = shutil.which("xdg-open")
    print(f"xdg-open:           {'OK' if xdg_open else 'MISSING (browser auto-open disabled)'}")

    try:
        import gi  # noqa: F401

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401

        print("GTK 3:              OK (tray available)")
    except Exception as err:
        print(f"GTK 3:              MISSING ({err}) -- tray disabled, web UI still works")

    print()
    print(f"Web UI will listen on 127.0.0.1:{options.web_port}")
    print(f"Thinking proxy on 127.0.0.1:{options.proxy_config.listen_port}")
    print(f"Upstream backend  127.0.0.1:{options.proxy_config.upstream_port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
