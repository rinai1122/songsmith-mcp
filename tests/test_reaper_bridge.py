"""Regression tests for the ReaperBridge offline-degradation behaviour.

The bridge must *never* block the MCP server. A stuck ``reapy.Project()``
handshake (observed on Windows when REAPER is closed but reapy thinks it
should be reachable) used to hang ``reaper_status`` for minutes — now it
times out after ~2 s and the bridge flips to offline mode.
"""

from __future__ import annotations

import time

import pytest

import songsmith_mcp.reaper_bridge as rb


@pytest.fixture(autouse=True)
def _reset_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGSMITH_OUT", str(tmp_path))
    rb._BRIDGE = None
    yield
    rb._BRIDGE = None


def test_connect_timeout_short_circuits_when_reapy_hangs(monkeypatch):
    # Pretend reapy is installed and *not* disabled by the env flag.
    monkeypatch.setattr(rb, "_HAVE_REAPY", True)
    monkeypatch.setattr(rb, "_DISABLE_REAPER", False)

    class _HangingProject:
        def __init__(self) -> None:
            # Simulate the real bug: constructor blocks indefinitely.
            time.sleep(10)

    class _FakeReapy:
        Project = _HangingProject

    monkeypatch.setattr(rb, "reapy", _FakeReapy)
    # Tight timeout so the test is fast.
    monkeypatch.setattr(rb.ReaperBridge, "_CONNECT_TIMEOUT_S", 0.3)

    t0 = time.monotonic()
    bridge = rb.ReaperBridge()
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, f"bridge init took {elapsed:.1f}s — timeout didn't fire"
    assert bridge.connected is False
    status = bridge.status()
    assert status["reaper_connected"] is False
    assert status["reaper_connect_timed_out"] is True


def test_connect_succeeds_when_reapy_returns_quickly(monkeypatch):
    monkeypatch.setattr(rb, "_HAVE_REAPY", True)
    monkeypatch.setattr(rb, "_DISABLE_REAPER", False)

    class _FakeProject:
        id = "FAKE"

    class _FakeReapy:
        Project = _FakeProject

    monkeypatch.setattr(rb, "reapy", _FakeReapy)
    bridge = rb.ReaperBridge()

    assert bridge.connected is True
    assert bridge.status()["reaper_connect_timed_out"] is False
