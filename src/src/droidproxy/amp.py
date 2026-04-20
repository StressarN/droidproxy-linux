"""Helpers shared by :mod:`droidproxy.proxy` for the Amp CLI forwarder.

The thinking proxy forwards any request that is neither ``/api/provider/*``
nor ``/v1/*`` / ``/api/v1/*`` to https://ampcode.com so Amp CLI login flows
keep working (the Swift app does the same). Header rewrites are applied to
the response so the browser keeps cookies scoped to ``localhost``.

This module centralises the header-rewrite logic so the same helpers can be
reused if we add a thin test for it without reaching into
:class:`droidproxy.proxy.ThinkingProxy`.
"""

from __future__ import annotations

from collections.abc import Iterable


def rewrite_amp_response_headers(
    headers: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Apply the Amp CLI Location + cookie-domain rewrites.

    Matches the Swift substring replacements in
    ``ThinkingProxy.receiveAmpResponse``.
    """
    rewritten: list[tuple[str, str]] = []
    for name, value in headers:
        lower = name.lower()
        if lower == "location":
            if value.startswith("/") and not value.startswith("/api/"):
                value = "/api" + value
            elif value.startswith("https://ampcode.com/") or value.startswith(
                "http://ampcode.com/"
            ):
                tail_part = value.split("ampcode.com", 1)[1]
                value = "/api" + tail_part
        elif lower == "set-cookie":
            value = value.replace("Domain=.ampcode.com", "Domain=localhost")
            value = value.replace("Domain=ampcode.com", "Domain=localhost")
        rewritten.append((name, value))
    return rewritten


def amp_cli_login_target(path: str, upstream: str = "https://ampcode.com") -> str:
    """Build the 302 redirect target for Amp CLI login paths.

    Used when the incoming path starts with ``/auth/cli-login`` or
    ``/api/auth/cli-login``. The ``/api`` prefix is stripped so the browser
    lands on the canonical ampcode.com URL.
    """
    login_path = path[len("/api"):] if path.startswith("/api/") else path
    return f"{upstream}{login_path}"
