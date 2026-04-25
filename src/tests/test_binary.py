from __future__ import annotations

import hashlib

import pytest

from droidproxy import binary as binary_module
from droidproxy.binary import (
    BinaryError,
    _parse_checksums,
    asset_name,
    detect_arch,
    release_url,
    verify_sha256,
)


def test_detect_arch_recognises_common_linux_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    for raw, expected in (("x86_64", "amd64"), ("aarch64", "arm64"), ("amd64", "amd64")):
        monkeypatch.setattr(binary_module.platform, "machine", lambda raw=raw: raw)
        assert detect_arch() == expected


def test_detect_arch_rejects_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_module.platform, "machine", lambda: "riscv64")
    with pytest.raises(BinaryError):
        detect_arch()


def test_asset_name_and_url_format() -> None:
    assert asset_name("1.2.3", arch="amd64") == "CLIProxyAPI_1.2.3_linux_amd64.tar.gz"
    assert release_url("1.2.3-0", arch="amd64").endswith(
        "/releases/download/v1.2.3-0/CLIProxyAPI_1.2.3-0_linux_amd64.tar.gz"
    )


def test_parse_checksums_handles_star_prefix_and_comments() -> None:
    text = (
        "# comment line\n"
        "abc123  CLIProxyAPI_1.0_linux_amd64.tar.gz\n"
        "deadbeef *CLIProxyAPI_1.0_linux_arm64.tar.gz\n"
        "\n"
    )
    parsed = _parse_checksums(text)
    assert parsed == {
        "CLIProxyAPI_1.0_linux_amd64.tar.gz": "abc123",
        "CLIProxyAPI_1.0_linux_arm64.tar.gz": "deadbeef",
    }


def test_verify_sha256_is_case_insensitive() -> None:
    payload = b"hello"
    digest = hashlib.sha256(payload).hexdigest()
    verify_sha256(payload, digest.upper())
    with pytest.raises(BinaryError):
        verify_sha256(payload, "0" * 64)
