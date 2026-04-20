"""Tests for the detach helpers in ``droidproxy.app``.

We don't fork inside pytest (it interacts badly with the test runner).
Instead we exercise the read/write/stop paths by using a real child
process that writes a pidfile, sleeps, and can be killed by SIGTERM.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from droidproxy import app as app_module


@pytest.fixture
def pidfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "droidproxy.pid"
    monkeypatch.setattr(app_module, "pidfile_path", lambda: target)
    return target


@pytest.fixture
def long_running_child() -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time, sys; sys.stdout.flush(); time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    yield proc
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def test_pidfile_path_respects_xdg_runtime_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert app_module.pidfile_path() == tmp_path / "droidproxy.pid"


def test_pidfile_path_falls_back_to_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    fake_state = tmp_path / "state"
    fake_state.mkdir()
    monkeypatch.setattr(app_module, "state_dir", lambda: fake_state)
    assert app_module.pidfile_path() == fake_state / "droidproxy.pid"


def test_status_reports_stopped_when_no_pidfile(pidfile: Path, capsys) -> None:
    assert not pidfile.exists()
    rc = app_module.daemon_status()
    assert rc == 3
    assert "stopped" in capsys.readouterr().out


def test_status_reports_stale_pidfile(pidfile: Path, capsys) -> None:
    pidfile.write_text("999999999\n")  # implausibly high pid
    rc = app_module.daemon_status()
    assert rc == 4
    assert "stale pidfile" in capsys.readouterr().out


def test_status_reports_running_for_live_pid(
    pidfile: Path,
    long_running_child: subprocess.Popen[bytes],
    capsys,
) -> None:
    pidfile.write_text(f"{long_running_child.pid}\n")
    rc = app_module.daemon_status()
    assert rc == 0
    out = capsys.readouterr().out
    assert "running" in out
    assert str(long_running_child.pid) in out


def test_stop_returns_1_when_not_running(pidfile: Path, capsys) -> None:
    assert not pidfile.exists()
    rc = app_module.stop_daemon()
    assert rc == 1


def test_stop_cleans_stale_pidfile(pidfile: Path, capsys) -> None:
    pidfile.write_text("999999999\n")
    rc = app_module.stop_daemon()
    assert rc == 1
    assert not pidfile.exists()


def test_stop_sends_sigterm_and_waits(
    pidfile: Path,
    long_running_child: subprocess.Popen[bytes],
) -> None:
    pidfile.write_text(f"{long_running_child.pid}\n")
    rc = app_module.stop_daemon(timeout=5.0)
    assert rc == 0
    assert not pidfile.exists()
    # Child should be dead; Popen.wait should return quickly.
    assert long_running_child.wait(timeout=1) is not None


def test_stop_force_kills_after_timeout(pidfile: Path, tmp_path: Path) -> None:
    # Child that ignores SIGTERM so we can test the escalation path. We
    # touch a marker file before sleeping so the test can wait for the
    # handler to actually be installed before stop_daemon runs -- without
    # this, Python's interpreter startup races the SIGTERM we send and the
    # child dies cleanly.
    marker = tmp_path / "ready"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import signal, time, pathlib, sys;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                f"pathlib.Path({str(marker)!r}).touch();"
                "time.sleep(60)"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "child never signalled readiness"

        pidfile.write_text(f"{proc.pid}\n")
        rc = app_module.stop_daemon(timeout=0.5)
        assert rc == 2
        assert not pidfile.exists()
        assert proc.wait(timeout=2) is not None
    finally:
        if proc.returncode is None:
            proc.kill()


def test_write_pidfile_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "sub" / "droidproxy.pid"
    monkeypatch.setattr(app_module, "pidfile_path", lambda: target)
    app_module._write_pidfile(12345)
    assert target.read_text().strip() == "12345"
    assert not (target.parent / "droidproxy.pid.tmp").exists()
